from app.models.schemas import (
    ContextFacility,
    Coordinates,
    FilterDesignSettings,
    OsmContext,
)
from app.services.design.filter_design import FilterDesigner
from app.services.impact.emergency_access import EmergencyAccessChecker
from tests.test_boundary_impact import cell_graph, cell_input, closures

STATION = ContextFacility(
    osm_type="node",
    osm_id=1,
    kind="fire_station",
    name="Station I",
    coordinates=Coordinates(lat=0.0, lon=0.0),
)


def designed(emergency_passable: bool):
    return FilterDesigner(
        cell_graph(),
        OsmContext(),
        FilterDesignSettings(emergency_passable_filters=emergency_passable),
    ).design(closures())


def test_emergency_bollards_keep_response_times():
    summary = EmergencyAccessChecker(cell_graph(), [STATION]).check(
        [cell_input(designed(True))], designed(True)
    )
    result = summary.results[0]
    assert summary.checked and summary.stations_used == 1
    assert result.nearest_station == "Station I"
    assert result.planned_seconds == result.baseline_seconds
    # Fully filtered centre node is unreachable if bollards stay locked.
    assert result.planned_without_exemption_seconds is None
    assert summary.superblocks_over_threshold == 0
    assert summary.superblocks_over_threshold_without_exemption == 1


def test_locked_filters_are_reported_and_missing_stations_skip():
    summary = EmergencyAccessChecker(cell_graph(), [STATION]).check(
        [cell_input(designed(False))], designed(False)
    )
    assert summary.unreachable_superblocks == 1
    assert summary.superblocks_over_threshold == 1

    skipped = EmergencyAccessChecker(cell_graph(), []).check([cell_input([])], [])
    assert skipped.checked is False
    assert skipped.limitations


def test_partition_reports_filter_design_and_emergency_access():
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
        osm_context=OsmContext(facilities=[STATION]),
    ).partition()

    assert result.osm_context is not None
    assert result.filter_design.transit_routes_loaded == 0
    assert result.emergency_access.checked is True
    assert result.emergency_access.stations_used == 1
