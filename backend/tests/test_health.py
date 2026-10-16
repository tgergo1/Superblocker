import pytest

from app.models.schemas import (
    BoundaryRoadImpact,
    SuperblockTrafficImpact,
    TrafficImpactEstimate,
)
from app.services.impact.health import HealthEstimator, volume_noise_change_db


def impact(reduction_percent: float, boundary_increase_percent: float) -> TrafficImpactEstimate:
    return TrafficImpactEstimate(
        superblocks=[
            SuperblockTrafficImpact(
                superblock_id="sb_1",
                entry_pairs_checked=4,
                cut_through_pairs=4,
                unresolved_pairs=0,
                displaced_peak_vph=200.0,
                interior_traffic_reduction_percent=reduction_percent,
                mean_detour_seconds=30.0,
            )
        ],
        boundary_roads=[
            BoundaryRoadImpact(
                osm_id=1,
                baseline_peak_vph=800.0,
                capacity_vph=1600.0,
                percent_increase_central=boundary_increase_percent,
                baseline_volume_capacity_ratio=0.5,
                planned_volume_capacity_ratio_central=0.6,
            )
        ],
    )


def test_halving_traffic_is_about_three_db_quieter():
    assert volume_noise_change_db(0.5) == pytest.approx(-3.01, abs=0.01)
    # Access traffic keeps running, so a full cut is floored at 5% remaining.
    assert volume_noise_change_db(0.0) == pytest.approx(-13.0, abs=0.1)


def test_interior_gets_quieter_and_cleaner_with_speed_range():
    result = HealthEstimator().estimate(impact(50.0, 0.0)).superblocks[0]

    assert result.interior_noise_change_db_low == pytest.approx(-4.0)
    assert result.interior_noise_change_db_high == pytest.approx(-6.0)
    assert result.interior_no2_change_percent_low == pytest.approx(-15.0)
    assert result.interior_no2_change_percent_high == pytest.approx(-30.0)


def test_boundary_roads_that_double_their_traffic_are_flagged():
    quiet = HealthEstimator().estimate(impact(50.0, 50.0))
    doubled = HealthEstimator().estimate(impact(50.0, 100.0))

    assert quiet.boundary_roads_noticeably_louder == 0
    assert quiet.maximum_boundary_noise_increase_db == pytest.approx(1.8)
    assert doubled.boundary_roads_noticeably_louder == 1
    assert doubled.maximum_boundary_noise_increase_db == pytest.approx(3.0)


def test_partition_result_carries_the_health_estimate():
    from app.models.schemas import BoundingBox
    from app.services.partitioning.city_partitioner import CityPartitioner
    from tests.test_routing_and_partitioning import partition_graph

    result = CityPartitioner(
        partition_graph(),
        BoundingBox(north=0.002, south=0, east=0.002, west=0),
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
    ).partition()

    assert result.health is not None
    assert len(result.health.superblocks) == len(result.traffic_impact.superblocks)
    assert result.health.limitations
