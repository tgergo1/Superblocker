import { useState, useCallback, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  analyzeAreaWithProgress,
  type AnalyzeResponse,
  type AnalysisProgress,
} from '../services/api';
import type { BoundingBox } from '../types';

// Analysis stage information for detailed progress tracking
export interface AnalysisStageInfo {
  stage: AnalysisProgress['stage'];
  stepNumber: number;
  totalSteps: number;
  stepName: string;
  stepDescription: string;
  estimatedDuration: number; // seconds
}

export const ANALYSIS_STAGES: Record<AnalysisProgress['stage'], AnalysisStageInfo> = {
  idle: { stage: 'idle', stepNumber: 0, totalSteps: 6, stepName: 'Ready', stepDescription: 'Waiting to start analysis', estimatedDuration: 0 },
  network: { stage: 'network', stepNumber: 1, totalSteps: 6, stepName: 'Fetching Network', stepDescription: 'Downloading street network from OpenStreetMap', estimatedDuration: 15 },
  centrality: { stage: 'centrality', stepNumber: 2, totalSteps: 6, stepName: 'Computing Centrality', stepDescription: 'Calculating betweenness centrality to identify through-traffic corridors', estimatedDuration: 30 },
  detection: { stage: 'detection', stepNumber: 3, totalSteps: 6, stepName: 'Detecting Cells', stepDescription: 'Identifying superblock candidate areas from road network', estimatedDuration: 20 },
  scoring: { stage: 'scoring', stepNumber: 4, totalSteps: 6, stepName: 'Scoring Candidates', stepDescription: 'Evaluating candidates on size, shape, traffic, accessibility', estimatedDuration: 15 },
  reorientation: { stage: 'reorientation', stepNumber: 5, totalSteps: 6, stepName: 'Planning Interventions', stepDescription: 'Designing street reorientations and modal filters', estimatedDuration: 10 },
  complete: { stage: 'complete', stepNumber: 6, totalSteps: 6, stepName: 'Complete', stepDescription: 'Analysis finished', estimatedDuration: 0 },
};

export interface AnalysisParameters {
  minAreaHectares: number;
  maxAreaHectares: number;
}

export interface DetailedProgress extends AnalysisProgress {
  stageInfo: AnalysisStageInfo;
  elapsedTime: number; // seconds
  estimatedRemainingTime: number; // seconds
  startTime: number | null;
}

export function useSuperblocks(bbox: BoundingBox | null) {
  const [parameters, setParameters] = useState<AnalysisParameters>({
    minAreaHectares: 4,
    maxAreaHectares: 25,
  });

  const startTimeRef = useRef<number | null>(null);
  const [progress, setProgress] = useState<DetailedProgress>({
    stage: 'idle',
    percent: 0,
    message: '',
    stageInfo: ANALYSIS_STAGES.idle,
    elapsedTime: 0,
    estimatedRemainingTime: 0,
    startTime: null,
  });

  // Calculate elapsed and remaining time
  const calculateTimes = useCallback((_stage: AnalysisProgress['stage'], percent: number): { elapsed: number; remaining: number } => {
    const startTime = startTimeRef.current;
    if (!startTime) return { elapsed: 0, remaining: 0 };
    
    const elapsed = (Date.now() - startTime) / 1000;
    
    // Estimate remaining time based on progress
    if (percent <= 0) return { elapsed, remaining: 90 }; // Default estimate
    if (percent === 100) return { elapsed, remaining: 0 };
    
    // Simple linear extrapolation
    const totalEstimate = elapsed / (percent / 100);
    const remaining = Math.max(0, totalEstimate - elapsed);
    
    return { elapsed: Math.round(elapsed), remaining: Math.round(remaining) };
  }, []);

  const mutation = useMutation<AnalyzeResponse, Error, void>({
    mutationFn: async () => {
      if (!bbox) throw new Error('No bounding box provided');

      startTimeRef.current = Date.now();
      setProgress({
        stage: 'network',
        percent: 0,
        message: 'Starting analysis...',
        stageInfo: ANALYSIS_STAGES.network,
        elapsedTime: 0,
        estimatedRemainingTime: 90, // Initial estimate
        startTime: startTimeRef.current,
      });

      return analyzeAreaWithProgress(bbox, {
        minAreaHectares: parameters.minAreaHectares,
        maxAreaHectares: parameters.maxAreaHectares,
        onProgress: (p) => {
          const times = calculateTimes(p.stage, p.percent);
          setProgress({
            ...p,
            stageInfo: ANALYSIS_STAGES[p.stage],
            elapsedTime: times.elapsed,
            estimatedRemainingTime: times.remaining,
            startTime: startTimeRef.current,
          });
        },
      });
    },
    onSuccess: () => {
      const times = calculateTimes('complete', 100);
      setProgress({
        stage: 'complete',
        percent: 100,
        message: 'Analysis complete',
        stageInfo: ANALYSIS_STAGES.complete,
        elapsedTime: times.elapsed,
        estimatedRemainingTime: 0,
        startTime: startTimeRef.current,
      });
    },
    onError: () => {
      setProgress({
        stage: 'idle',
        percent: 0,
        message: '',
        stageInfo: ANALYSIS_STAGES.idle,
        elapsedTime: 0,
        estimatedRemainingTime: 0,
        startTime: null,
      });
      startTimeRef.current = null;
    },
  });

  const analyze = useCallback(() => {
    if (bbox) {
      mutation.mutate();
    }
  }, [bbox, mutation]);

  const reset = useCallback(() => {
    mutation.reset();
    startTimeRef.current = null;
    setProgress({
      stage: 'idle',
      percent: 0,
      message: '',
      stageInfo: ANALYSIS_STAGES.idle,
      elapsedTime: 0,
      estimatedRemainingTime: 0,
      startTime: null,
    });
  }, [mutation]);

  return {
    data: mutation.data,
    isLoading: mutation.isPending,
    error: mutation.error,
    progress,
    parameters,
    setParameters,
    analyze,
    reset,
  };
}
