import { useState, useCallback, useEffect, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  partitionCityWithProgress,
  type PartitionRequest,
  type PartitionResponse,
} from '../services/api';
import type { BoundingBox, PartitionProgress, CityPartition } from '../types';

// Partition stage information for detailed progress tracking
export interface PartitionStageInfo {
  stage: PartitionProgress['stage'];
  stepNumber: number;
  totalSteps: number;
  stepName: string;
  stepDescription: string;
}

export const PARTITION_STAGES: Record<PartitionProgress['stage'], PartitionStageInfo> = {
  network: { stage: 'network', stepNumber: 1, totalSteps: 6, stepName: 'Preparing Network', stepDescription: 'Loading and preparing street network data' },
  arterials: { stage: 'arterials', stepNumber: 2, totalSteps: 6, stepName: 'Identifying Arterials', stepDescription: 'Finding main roads that will form superblock boundaries' },
  cells: { stage: 'cells', stepNumber: 3, totalSteps: 6, stepName: 'Creating Cells', stepDescription: 'Polygonizing arterial network to create superblock cells' },
  constraints: { stage: 'constraints', stepNumber: 4, totalSteps: 6, stepName: 'Enforcing Constraints', stepDescription: 'Computing minimum edge cuts to enforce enter-exit rules' },
  validation: { stage: 'validation', stepNumber: 5, totalSteps: 6, stepName: 'Validating', stepDescription: 'Checking accessibility and constraint satisfaction' },
  complete: { stage: 'complete', stepNumber: 6, totalSteps: 6, stepName: 'Complete', stepDescription: 'Partitioning finished' },
};

export interface PartitionParameters {
  targetSizeHectares: number;
  minAreaHectares: number;
  maxAreaHectares: number;
}

export interface DetailedPartitionProgress extends PartitionProgress {
  stageInfo: PartitionStageInfo;
  elapsedTime: number;
  estimatedRemainingTime: number;
  startTime: number | null;
}

export function usePartition(bbox: BoundingBox | null) {
  const [parameters, setParameters] = useState<PartitionParameters>({
    targetSizeHectares: 12,
    minAreaHectares: 6,
    maxAreaHectares: 20,
  });

  const startTimeRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<DetailedPartitionProgress>({
    stage: 'network',
    percent: 0,
    message: '',
    stageInfo: PARTITION_STAGES.network,
    elapsedTime: 0,
    estimatedRemainingTime: 0,
    startTime: null,
  });

  // Track the partition result separately to persist it
  const [partition, setPartition] = useState<CityPartition | null>(null);

  const calculateTimes = useCallback((percent: number): { elapsed: number; remaining: number } => {
    const startTime = startTimeRef.current;
    if (!startTime) return { elapsed: 0, remaining: 0 };

    const elapsed = (Date.now() - startTime) / 1000;

    if (percent <= 0) return { elapsed, remaining: 120 };
    if (percent >= 100) return { elapsed, remaining: 0 };

    const totalEstimate = elapsed / (percent / 100);
    const remaining = Math.max(0, totalEstimate - elapsed);

    return { elapsed: Math.round(elapsed), remaining: Math.round(remaining) };
  }, []);

  const mutation = useMutation<PartitionResponse, Error, void>({
    mutationFn: async () => {
      if (!bbox) throw new Error('No bounding box provided');

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      startTimeRef.current = Date.now();
      setProgress({
        stage: 'network',
        percent: 0,
        message: 'Starting partition...',
        stageInfo: PARTITION_STAGES.network,
        elapsedTime: 0,
        estimatedRemainingTime: 120,
        startTime: startTimeRef.current,
      });

      const request: PartitionRequest = {
        bbox,
        target_size_hectares: parameters.targetSizeHectares,
        min_area_hectares: parameters.minAreaHectares,
        max_area_hectares: parameters.maxAreaHectares,
        // Four cardinal entry/return sectors are a product invariant.
        num_sectors: 4,
      };

      return partitionCityWithProgress(request, (p) => {
        const times = calculateTimes(p.percent);
        const stageKey = p.stage as PartitionProgress['stage'];
        setProgress({
          ...p,
          stageInfo: PARTITION_STAGES[stageKey] || PARTITION_STAGES.network,
          elapsedTime: times.elapsed,
          estimatedRemainingTime: times.remaining,
          startTime: startTimeRef.current,
        });
      }, controller.signal);
    },
    onSuccess: (data) => {
      const times = calculateTimes(100);
      setProgress({
        stage: 'complete',
        percent: 100,
        message: 'Partition complete',
        stageInfo: PARTITION_STAGES.complete,
        elapsedTime: times.elapsed,
        estimatedRemainingTime: 0,
        startTime: startTimeRef.current,
      });
      // Store the partition result
      setPartition(data.partition);
    },
    onError: (error) => {
      if (error.name === 'AbortError') return;
      setProgress({
        stage: 'network',
        percent: 0,
        message: error.message || 'Partition failed',
        stageInfo: PARTITION_STAGES.network,
        elapsedTime: 0,
        estimatedRemainingTime: 0,
        startTime: null,
      });
      startTimeRef.current = null;
    },
  });

  const runPartition = useCallback(() => {
    if (bbox) {
      mutation.mutate();
    }
  }, [bbox, mutation]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    startTimeRef.current = null;
    setProgress({
      stage: 'network',
      percent: 0,
      message: 'Partition cancelled',
      stageInfo: PARTITION_STAGES.network,
      elapsedTime: 0,
      estimatedRemainingTime: 0,
      startTime: null,
    });
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    mutation.reset();
    startTimeRef.current = null;
    setPartition(null);
    setProgress({
      stage: 'network',
      percent: 0,
      message: '',
      stageInfo: PARTITION_STAGES.network,
      elapsedTime: 0,
      estimatedRemainingTime: 0,
      startTime: null,
    });
  }, [mutation]);

  return {
    data: mutation.data,
    partition, // The persisted partition result
    isLoading: mutation.isPending,
    error: mutation.error?.name === 'AbortError' ? null : mutation.error,
    progress,
    parameters,
    setParameters,
    runPartition,
    cancel,
    reset,
  };
}
