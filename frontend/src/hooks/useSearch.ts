import { useState, useCallback } from 'react';
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

  return {
    query,
    searchResults: searchResults?.results ?? [],
    isLoading,
    error,
    selectedPlace,
    handleSearch,
    handleSelect,
    clearSelection,
    refetch,
  };
}
