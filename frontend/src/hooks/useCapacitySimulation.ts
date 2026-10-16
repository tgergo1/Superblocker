import { useCallback, useEffect, useRef, useState } from 'react';
import { simulateCapacityWithProgress } from '../services/api';
import type { CityPartition } from '../types';
import type { CapacitySimulationResult, SimulationProgress } from '../types/simulation';

export function useCapacitySimulation(partition: CityPartition | null) {
  const [result, setResult] = useState<CapacitySimulationResult | null>(null);
  const [progress, setProgress] = useState<SimulationProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setIsRunning(false);
    setProgress(null);
  }, []);

  // A new plan makes the previous comparison stale.
  useEffect(() => {
    cancel();
    setResult(null);
    setError(null);
  }, [partition, cancel]);

  const run = useCallback(
    async (peakHourTrips: number | null) => {
      if (!partition) return;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      setIsRunning(true);
      setError(null);
      setProgress(null);
      try {
        const simulation = await simulateCapacityWithProgress(
          partition,
          peakHourTrips ? { peak_hour_trips: peakHourTrips } : {},
          setProgress,
          abort.signal,
        );
        if (!abort.signal.aborted) setResult(simulation);
      } catch (caught) {
        if (!abort.signal.aborted) {
          setError(caught instanceof Error ? caught.message : 'The simulation failed');
        }
      } finally {
        if (controller.current === abort) {
          controller.current = null;
          setIsRunning(false);
          setProgress(null);
        }
      }
    },
    [partition],
  );

  return { result, progress, error, isRunning, run, cancel };
}
