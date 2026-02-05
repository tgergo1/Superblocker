import { useState, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  analyzeAreaWithProgress,
  type AnalyzeResponse,
  type AnalysisProgress,
} from '../services/api';
import type { BoundingBox } from '../types';

export function useSuperblocks(bbox: BoundingBox | null) {
  const [progress, setProgress] = useState<AnalysisProgress>({
    stage: 'idle',
    percent: 0,
    message: '',
  });

  const mutation = useMutation<AnalyzeResponse, Error, void>({
    mutationFn: async () => {
      if (!bbox) throw new Error('No bounding box provided');

      setProgress({ stage: 'network', percent: 0, message: 'Starting analysis...' });

      return analyzeAreaWithProgress(bbox, {
        onProgress: (p) => setProgress(p),
      });
    },
    onSuccess: () => {
      setProgress({ stage: 'complete', percent: 100, message: 'Analysis complete' });
    },
    onError: () => {
      setProgress({ stage: 'idle', percent: 0, message: '' });
    },
  });

  const analyze = useCallback(() => {
    if (bbox) {
      mutation.mutate();
    }
  }, [bbox, mutation]);

  const reset = useCallback(() => {
    mutation.reset();
    setProgress({ stage: 'idle', percent: 0, message: '' });
  }, [mutation]);

  return {
    data: mutation.data,
    isLoading: mutation.isPending,
    error: mutation.error,
    progress,
    analyze,
    reset,
  };
}
