import { useState, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchPlaces } from '../services/api';
import type { SearchResult } from '../types';

export function useSearch() {
  const [query, setQuery] = useState('');
  const [selectedPlace, setSelectedPlace] = useState<SearchResult | null>(null);

  const {
    data: searchResults,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['search', query],
    queryFn: () => searchPlaces(query),
    enabled: query.length >= 2,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  const handleSearch = useCallback((newQuery: string) => {
    setQuery(newQuery);
  }, []);

  const handleSelect = useCallback((place: SearchResult) => {
    setSelectedPlace(place);
    setQuery('');
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedPlace(null);
  }, []);

  const uniqueSearchResults = useMemo(() => {
    const seen = new Set<string>();
    return (searchResults?.results ?? []).filter((result) => {
      const key = result.display_name.trim().toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [searchResults?.results]);

  return {
    query,
    searchResults: uniqueSearchResults,
    isLoading,
    error,
    selectedPlace,
    handleSearch,
    handleSelect,
    clearSelection,
    refetch,
  };
}
