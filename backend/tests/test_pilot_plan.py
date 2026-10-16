import pytest

from app.models.schemas import (
    EmergencyAccessResult,
    EmergencyAccessSummary,
    ModificationType,
    StreetModification,
    SuperblockTrafficImpact,
    TrafficImpactEstimate,
)
from app.services.design.pilot_plan import PilotPlanner, build_cost, trial_cost
from app.services.impact.boundary_impact import SuperblockImpactInput


def block(sid, filters):
    return SuperblockImpactInput(
        superblock_id=sid,
        interior_edges=[],
        boundary_edges=[],
        entry_sectors={},
        modifications=[
            StreetModification(
                u=i,
                v=i + 1,
                osm_id=i + 1,
                modification_type=ModificationType.MODAL_FILTER,
                filter_type="emergency_bollard",
            )
            for i in range(filters)
        ],
    )


def traffic(sid, displaced, reduction, detour=0.0):
    return SuperblockTrafficImpact(
        superblock_id=sid,
        entry_pairs_checked=10,
        cut_through_pairs=5,
        unresolved_pairs=0,
        displaced_peak_vph=displaced,
        interior_traffic_reduction_percent=reduction,
        mean_detour_seconds=detour,
    )


def test_costs_follow_filter_type_and_explicit_estimates():
    bollard = block("a", 1).modifications[0]
    assert trial_cost(bollard) == 2_500
    assert build_cost(bollard) == 8_000
    priced = bollard.model_copy(update={"estimated_capital_cost": 12_345})
    assert build_cost(priced) == 12_345


def test_ranks_by_benefit_and_penalises_slow_emergency_response():
    blocks = [block("big", 2), block("small", 1), block("slow", 2)]
    impact = TrafficImpactEstimate(
        superblocks=[traffic("big", 400, 60), traffic("small", 100, 20), traffic("slow", 400, 60)]
    )
    emergency = EmergencyAccessSummary(
        checked=True,
        results=[
            EmergencyAccessResult(superblock_id="slow", baseline_seconds=100, planned_seconds=400),
            EmergencyAccessResult(superblock_id="big", baseline_seconds=100, planned_seconds=110),
        ],
    )
    summary = PilotPlanner().plan(
        blocks, impact, emergency_access=emergency, centroids={"big": (47.5, 19.05)}
    )
    order = [item.superblock_id for item in summary.candidates]
    assert order == ["big", "slow", "small"]
    top = summary.candidates[0]
    assert top.rank == 1
    assert top.centroid_lat == 47.5
    assert top.trial_cost_eur == 5_000
    assert top.emergency_delay_seconds == 10
    # 60% less traffic, then a 0.64-0.85 speed factor on what remains.
    assert top.interior_injury_reduction_low_percent == pytest.approx(66.0)
    assert top.interior_injury_reduction_high_percent == pytest.approx(74.4)
    assert summary.total_trial_cost_eur == 12_500
    assert summary.limitations


def test_top_n_limits_candidates():
    blocks = [block(f"b{i}", 1) for i in range(5)]
    summary = PilotPlanner(top_n=3).plan(blocks)
    assert len(summary.candidates) == 3
    assert summary.total_trial_cost_eur == 12_500
