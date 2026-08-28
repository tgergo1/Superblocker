import { useState, useCallback, useRef, useEffect } from 'react';
import type { SearchResult } from '../../types';
import './SearchBox.css';

interface SearchBoxProps {
  onSearch: (query: string) => void;
  onSelect: (place: SearchResult) => void;
  results: SearchResult[];
  isLoading: boolean;
  error?: Error | null;
  selectedPlace: SearchResult | null;
  onClear: () => void;
}

export function SearchBox({
  onSearch,
  onSelect,
  results,
  isLoading,
  error,
  selectedPlace,
  onClear,
}: SearchBoxProps) {
  const [inputValue, setInputValue] = useState('');
  const [showResults, setShowResults] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
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
      setShowResults(false);
      setActiveIndex(-1);
    },
    []
  );

  const handleSubmit = useCallback((event: React.FormEvent) => {
    event.preventDefault();
    const query = inputValue.trim();
    if (query.length < 2) return;
    setShowResults(true);
    setActiveIndex(-1);
    onSearch(query);
  }, [inputValue, onSearch]);

  const handleSelectResult = useCallback(
    (result: SearchResult) => {
      setInputValue('');
      setShowResults(false);
      setActiveIndex(-1);
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
    if (results.length > 0) {
      setShowResults(true);
    }
  }, [results.length]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showResults || results.length === 0) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault();
      handleSelectResult(results[activeIndex]);
    } else if (event.key === 'Escape') {
      setShowResults(false);
    }
  }, [activeIndex, handleSelectResult, results, showResults]);

  if (selectedPlace) {
    return (
      <div className="search-box selected" ref={containerRef}>
        <div className="selected-place">
          <span className="place-name">{selectedPlace.display_name}</span>
          <button className="clear-button" onClick={handleClear} aria-label="Clear selected place">
            &times;
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="search-box" ref={containerRef}>
      <form className="search-input-container" onSubmit={handleSubmit} role="search">
        <input
          ref={inputRef}
          type="text"
          className="search-input"
          placeholder="Search for a city or place..."
          value={inputValue}
          onChange={handleInputChange}
          onFocus={handleFocus}
          onKeyDown={handleKeyDown}
          aria-label="Search for a city or place"
          aria-expanded={showResults}
          aria-controls="place-search-results"
          aria-activedescendant={activeIndex >= 0 ? `place-result-${activeIndex}` : undefined}
          autoComplete="off"
        />
        {isLoading ? <div className="search-spinner" aria-label="Searching" /> : (
          <button type="submit" className="search-button" disabled={inputValue.trim().length < 2}>
            Search
          </button>
        )}
      </form>

      {showResults && results.length > 0 && (
        <ul className="search-results" id="place-search-results" role="listbox">
          {results.map((result, index) => (
            <li
              key={result.place_id}
              id={`place-result-${index}`}
              className={`search-result-item ${activeIndex === index ? 'active' : ''}`}
              role="option"
              aria-selected={activeIndex === index}
              onClick={() => handleSelectResult(result)}
              onMouseEnter={() => setActiveIndex(index)}
            >
              <span className="result-name">{result.display_name}</span>
              <span className="result-type">{result.type}</span>
            </li>
          ))}
        </ul>
      )}

      {showResults && error && !isLoading && (
        <div className="search-no-results search-error" role="alert">Place search failed. Please try again.</div>
      )}

      {showResults && !error && results.length === 0 && !isLoading && (
        <div className="search-no-results" role="status">No results found</div>
      )}
    </div>
  );
}
