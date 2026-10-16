import { useMemo, useState } from 'react';
import type {
  CityPartition,
  DemandModel,
  EngineeringPackage,
  ScenarioPortfolio,
  SimulationRun,
} from '../../types';
import {
  compileEngineeringPackage,
  exportSimulation,
  optimizePlanScenarios,
} from '../../services/api';
import './PlanningWorkbench.css';

interface PlanningWorkbenchProps {
  partition: CityPartition;
  demandModel: DemandModel | null;
  operationalRequirements: unknown[];
  activeModeRequirements: unknown[];
  selectedPlaceName: string;
}

type Workflow = 'scenarios' | 'engineering' | 'simulation' | null;

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function formatNumber(value: number | null, digits = 1): string {
  return value === null ? 'Not run' : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function PlanningWorkbench({
  partition,
  demandModel,
  operationalRequirements,
  activeModeRequirements,
  selectedPlaceName,
}: PlanningWorkbenchProps) {
  const [expanded, setExpanded] = useState(false);
  const [adminKey, setAdminKey] = useState('');
  const [jurisdiction, setJurisdiction] = useState(selectedPlaceName.split(',')[0] || 'Selected jurisdiction');
  const [executeSumo, setExecuteSumo] = useState(false);
  const [running, setRunning] = useState<Workflow>(null);
  const [error, setError] = useState<string | null>(null);
  const [portfolio, setPortfolio] = useState<ScenarioPortfolio | null>(null);
  const [engineeringPackage, setEngineeringPackage] = useState<EngineeringPackage | null>(null);
  const [simulation, setSimulation] = useState<SimulationRun | null>(null);
  const assignment = partition.assignment_comparison;
  const failedServices = partition.operational_validation.results.filter((item) => !item.passed);
  const failedActiveModes = partition.active_mode_validation.results.filter((item) => !item.passed);
  const maximumQueue = useMemo(() => Math.max(
    0,
    ...(assignment.planned?.edge_flows.map((flow) => flow.estimated_queue_length_m) ?? []),
  ), [assignment.planned]);

  const runWorkflow = async (workflow: Exclude<Workflow, null>) => {
    if (!adminKey.trim()) {
      setError('Enter the configured administrator API key. It is kept only in this page state.');
      return;
    }
    setRunning(workflow);
    setError(null);
    try {
      if (workflow === 'scenarios') {
        if (!demandModel) throw new Error('Load a planning evidence package with an OD demand model first.');
        setPortfolio(await optimizePlanScenarios(
          partition,
          demandModel,
          adminKey,
          operationalRequirements,
          activeModeRequirements,
        ));
      } else if (workflow === 'engineering') {
        setEngineeringPackage(await compileEngineeringPackage(partition, jurisdiction, adminKey));
      } else {
        if (!demandModel) throw new Error('Load a planning evidence package with an OD demand model first.');
        setSimulation(await exportSimulation(partition, demandModel, adminKey, executeSumo));
      }
    } catch (workflowError) {
      setError(workflowError instanceof Error ? workflowError.message : 'Workflow failed');
    } finally {
      setRunning(null);
    }
  };

  return (
    <section className="planning-workbench" aria-label="Plan validation workbench">
      <button
        type="button"
        className="workbench-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span>
          <strong>Validation workbench</strong>
          <small>Assignment, services, alternatives, SUMO, and field package</small>
        </span>
        <span aria-hidden="true">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="workbench-body">
          <div className="workbench-section">
            <h3>Network assignment</h3>
            <div className="metric-grid">
              <div><span>Demand assigned</span><strong>{assignment.all_demand_assigned ? 'Pass' : 'Not proven'}</strong></div>
              <div><span>Plan convergence</span><strong>{assignment.planned?.converged ? 'Pass' : 'Not run'}</strong></div>
              <div><span>Boundary max v/c</span><strong>{formatNumber(assignment.maximum_boundary_volume_capacity_ratio, 2)}</strong></div>
              <div><span>Max queue</span><strong>{maximumQueue ? `${formatNumber(maximumQueue)} m` : 'Not run'}</strong></div>
              <div><span>Adjacent change</span><strong>{assignment.maximum_adjacent_road_volume_change_percent === null ? 'Not run' : `${formatNumber(assignment.maximum_adjacent_road_volume_change_percent)}%`}</strong></div>
              <div><span>Blocked trips</span><strong>{formatNumber(assignment.planned?.blocked_demand ?? null, 0)}</strong></div>
            </div>
            {assignment.limitations.length > 0 && (
              <ul className="workbench-notes">
                {assignment.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            )}
          </div>

          <div className="workbench-section">
            <h3>Protected operations</h3>
            <div className="validation-lines">
              <div className={partition.operational_validation.all_hard_constraints_passed ? 'pass' : 'pending'}>
                <span>Transit, emergency, freight, refuse, permits</span>
                <strong>{partition.operational_validation.all_hard_constraints_passed ? 'Pass' : 'Incomplete'}</strong>
              </div>
              <div className={partition.active_mode_validation.all_hard_constraints_passed ? 'pass' : 'pending'}>
                <span>Walking, wheeling, cycling</span>
                <strong>{partition.active_mode_validation.all_hard_constraints_passed ? 'Pass' : 'Incomplete'}</strong>
              </div>
            </div>
            {[...partition.operational_validation.missing_service_types, ...partition.active_mode_validation.missing_modes].length > 0 && (
              <p className="missing-evidence">
                Missing requirements: {[...partition.operational_validation.missing_service_types, ...partition.active_mode_validation.missing_modes].join(', ')}
              </p>
            )}
            {[...failedServices, ...failedActiveModes].map((item) => (
              <p className="failed-requirement" key={item.requirement_id}>
                {item.requirement_id}: {item.blockers.join('; ')}
              </p>
            ))}
          </div>

          <div className="workbench-section workflow-section">
            <h3>Authenticated workflows</h3>
            <label>
              <span>Administrator API key</span>
              <input
                type="password"
                autoComplete="off"
                value={adminKey}
                onChange={(event) => setAdminKey(event.target.value)}
                placeholder="Required by the backend"
              />
            </label>
            <label>
              <span>Jurisdiction</span>
              <input value={jurisdiction} onChange={(event) => setJurisdiction(event.target.value)} />
            </label>
            <div className="workflow-buttons">
              <button type="button" disabled={running !== null || !demandModel} onClick={() => void runWorkflow('scenarios')}>
                {running === 'scenarios' ? 'Evaluating…' : 'Evaluate alternatives'}
              </button>
              <button type="button" disabled={running !== null} onClick={() => void runWorkflow('engineering')}>
                {running === 'engineering' ? 'Compiling…' : 'Compile field package'}
              </button>
              <button type="button" disabled={running !== null || !demandModel} onClick={() => void runWorkflow('simulation')}>
                {running === 'simulation' ? 'Working…' : executeSumo ? 'Run SUMO' : 'Export SUMO'}
              </button>
            </div>
            <label className="sumo-toggle">
              <input type="checkbox" checked={executeSumo} onChange={(event) => setExecuteSumo(event.target.checked)} />
              <span>Execute SUMO toolchain after export</span>
            </label>
            {!demandModel && <p className="missing-evidence">OD demand is required for alternatives and simulation.</p>}
            {error && <p className="workflow-error" role="alert">{error}</p>}
          </div>

          {portfolio && (
            <div className="workbench-section result-section">
              <div className="result-heading">
                <h3>Scenario portfolio</h3>
                <button type="button" onClick={() => downloadJson(`scenarios-${portfolio.portfolio_id}.json`, portfolio)}>Download</button>
              </div>
              {portfolio.candidates.map((candidate) => (
                <article key={candidate.scenario_id} className={candidate.feasible ? 'candidate feasible' : 'candidate blocked'}>
                  <strong>{candidate.name}</strong>
                  <span>{candidate.feasible ? `Pareto rank ${candidate.pareto_rank ?? '—'}` : `${candidate.hard_constraint_failures.length} hard failures`}</span>
                  {candidate.hard_constraint_failures.map((failure) => <small key={failure}>{failure}</small>)}
                </article>
              ))}
            </div>
          )}

          {engineeringPackage && (
            <div className="workbench-section result-section">
              <div className="result-heading">
                <h3>Draft field package</h3>
                <button type="button" onClick={() => downloadJson(`engineering-${engineeringPackage.package_id}.json`, engineeringPackage)}>Download</button>
              </div>
              <div className="package-total">
                <strong>{engineeringPackage.currency} {formatNumber(engineeringPackage.total_capital_cost, 0)}</strong>
                <span>{engineeringPackage.instructions.length} instructions · draft only</span>
              </div>
              {engineeringPackage.blockers.map((blocker) => <small className="package-blocker" key={blocker}>{blocker}</small>)}
            </div>
          )}

          {simulation && (
            <div className="workbench-section result-section">
              <div className="result-heading">
                <h3>SUMO {simulation.status}</h3>
                <button type="button" onClick={() => downloadJson(`simulation-${simulation.simulation_id}.json`, simulation)}>Download</button>
              </div>
              <p>{simulation.generated_files.length} files · seed {simulation.random_seed}</p>
              {simulation.limitations.map((item) => <small className="package-blocker" key={item}>{item}</small>)}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
