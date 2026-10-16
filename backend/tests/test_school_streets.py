from app.models.schemas import ContextFacility, Coordinates
from app.services.design.school_streets import SchoolStreetPlanner
from tests.test_boundary_impact import cell_graph, cell_input


def facility(osm_id, kind, lon, lat):
    return ContextFacility(
        osm_type="node",
        osm_id=osm_id,
        kind=kind,
        name=f"{kind} {osm_id}",
        coordinates=Coordinates(lat=lat, lon=lon),
    )


def test_schools_get_interior_school_streets_or_boundary_frontage():
    planner = SchoolStreetPlanner(cell_graph(), transit_way_ids={200})
    summary = planner.plan(
        [
            facility(1, "school", 0.0012, 0.0005),
            facility(2, "kindergarten", 0.0005, -0.0001),
            facility(3, "school", 0.02, 0.02),
            facility(4, "fire_station", 0.001, 0.001),
        ],
        [cell_input([])],
    )
    by_id = {item.facility_osm_id: item for item in summary.schools}
    assert summary.facilities_loaded == 3
    assert set(by_id) == {1, 2, 3}

    interior = by_id[1]
    assert interior.setting == "interior"
    assert interior.superblock_id == "sb_test"
    assert interior.osm_id == 200
    assert interior.on_transit_route
    assert interior.time_windows == ["Mo-Fr 07:30-08:30", "Mo-Fr 15:30-16:30"]
    assert "Buses keep access" in interior.treatment
    assert 15 < interior.distance_m < 30
    assert len(interior.street_path) == 2

    frontage = by_id[2]
    assert frontage.setting == "boundary"
    assert frontage.street_name == "Ring"
    assert frontage.time_windows == []
    assert "30 km/h" in frontage.treatment

    assert by_id[3].setting == "unassigned"
    assert summary.interior_school_streets == 1
    assert summary.boundary_frontages == 1
    assert summary.unassigned == 1


def test_without_school_layer_reports_limitation():
    summary = SchoolStreetPlanner(cell_graph()).plan([], [cell_input([])])
    assert summary.facilities_loaded == 0
    assert any("No school" in text for text in summary.limitations)
