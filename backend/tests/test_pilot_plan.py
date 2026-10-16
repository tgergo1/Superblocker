import pytest

from app.models.schemas import (
    EmergencyAccessResult,
    EmergencyAccessSummary,
    ModificationType,
    StreetModification,
    SuperblockTrafficImpact,
    SuperblockTransitAccess,
    TrafficImpactEstimate,
    TransitAccessSummary,
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


def test_poor_transit_access_lowers_the_rank():
    blocks = [block("served", 1), block("remote", 1)]
    impact = TrafficImpactEstimate(
        superblocks=[traffic("served", 300, 50), traffic("remote", 300, 50)]
    )
    transit = TransitAccessSummary(
        checked=True,
        superblocks=[
            SuperblockTransitAccess(superblock_id="served", covered_share=0.9),
            SuperblockTransitAccess(superblock_id="remote", covered_share=0.2),
        ],
    )
    without = PilotPlanner().plan(blocks, impact)
    assert without.candidates[0].score == without.candidates[1].score

    summary = PilotPlanner().plan(blocks, impact, transit_access=transit)
    served, remote = summary.candidates
    assert (served.superblock_id, remote.superblock_id) == ("served", "remote")
    assert remote.score == pytest.approx(served.score * 0.85, abs=0.1)
    assert (served.transit_covered_share, remote.transit_covered_share) == (0.9, 0.2)
    assert "public transport stop" in summary.limitations[-1]


def test_unchecked_transit_access_changes_nothing():
    blocks = [block("a", 1)]
    impact = TrafficImpactEstimate(superblocks=[traffic("a", 300, 50)])
    baseline = PilotPlanner().plan(blocks, impact).candidates[0]
    summary = PilotPlanner().plan(blocks, impact, transit_access=TransitAccessSummary())
    assert summary.candidates[0].score == baseline.score
    assert summary.candidates[0].transit_covered_share is None
    assert "public transport" not in summary.limitations[-1]
