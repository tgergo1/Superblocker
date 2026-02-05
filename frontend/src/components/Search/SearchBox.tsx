import { useState, useCallback, useRef, useEffect } from 'react';
import type { SearchResult } from '../../types';
import './SearchBox.css';

interface SearchBoxProps {
  onSearch: (query: string) => void;
  onSelect: (place: SearchResult) => void;
  results: SearchResult[];
  isLoading: boolean;
  selectedPlace: SearchResult | null;
  onClear: () => void;
}

export function SearchBox({
  onSearch,
  onSelect,
  results,
  isLoading,
  selectedPlace,
  onClear,
}: SearchBoxProps) {
  const [inputValue, setInputValue] = useState('');
  const [showResults, setShowResults] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setShowResults(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setInputValue(value);
      setShowResults(true);
      onSearch(value);
    },
    [onSearch]
  );

  const handleSelectResult = useCallback(
    (result: SearchResult) => {
      setInputValue('');
      setShowResults(false);
      onSelect(result);
    },
    [onSelect]
  );

  const handleClear = useCallback(() => {
    setInputValue('');
    setShowResults(false);
    onClear();
    inputRef.current?.focus();
  }, [onClear]);

  const handleFocus = useCallback(() => {
    if (inputValue.length >= 2) {
      setShowResults(true);
    }
  }, [inputValue]);

  if (selectedPlace) {
    return (
      <div className="search-box selected" ref={containerRef}>
        <div className="selected-place">
          <span className="place-name">{selectedPlace.display_name}</span>
          <button className="clear-button" onClick={handleClear} title="Clear selection">
            &times;
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="search-box" ref={containerRef}>
      <div className="search-input-container">
        <input
          ref={inputRef}
          type="text"
          className="search-input"
          placeholder="Search for a city or place..."
          value={inputValue}
          onChange={handleInputChange}
          onFocus={handleFocus}
        />
        {isLoading && <div className="search-spinner" />}
      </div>

      {showResults && results.length > 0 && (
        <ul className="search-results">
          {results.map((result) => (
            <li
              key={result.place_id}
              className="search-result-item"
              onClick={() => handleSelectResult(result)}
            >
              <span className="result-name">{result.display_name}</span>
              <span className="result-type">{result.type}</span>
            </li>
          ))}
        </ul>
      )}

      {showResults && inputValue.length >= 2 && results.length === 0 && !isLoading && (
        <div className="search-no-results">No results found</div>
      )}
    </div>
  );
}
