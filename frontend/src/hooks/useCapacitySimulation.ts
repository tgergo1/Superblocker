import { useCallback, useEffect, useRef, useState } from 'react';
import { simulateCapacityWithProgress } from '../services/api';
import type { CityPartition } from '../types';
import { BUDAPEST_CORDON, BUDAPEST_CORDON_NAME } from '../data/budapestCordon';
import type {
  CapacitySimulationResult,
  CapacitySimulationSettings,
  SimulationProgress,
} from '../types/simulation';

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
    async (peakHourTrips: number | null, entryCharge: number | null = null) => {
      if (!partition) return;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      setIsRunning(true);
      setError(null);
      setProgress(null);
      try {
        const settings: CapacitySimulationSettings = {};
        if (peakHourTrips) settings.peak_hour_trips = peakHourTrips;
        if (entryCharge) {
          settings.entry_charge = {
            name: BUDAPEST_CORDON_NAME,
            cordon: BUDAPEST_CORDON,
            charge: entryCharge,
          };
        }
        const simulation = await simulateCapacityWithProgress(
          partition,
          settings,
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
