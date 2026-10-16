from app.models.schemas import (
    AccessTarget,
    BoundingBox,
    Coordinates,
    ModificationType,
    StreetModification,
    VehicleClass,
)
from app.services.partitioning.city_partitioner import CityPartitioner
from tests.test_boundary_impact import cell_graph

BBOX = BoundingBox(north=0.003, south=-0.001, east=0.003, west=-0.001)
CENTRE = 6


def target(**constraints) -> AccessTarget:
    return AccessTarget(
        id="bay-1",
        coordinates=Coordinates(lat=0.001, lon=0.001),
        kind="delivery",
        source="Survey",
        **constraints,
    )


def failures(modifications, entries, **constraints):
    graph = cell_graph()
    partitioner = CityPartitioner(graph, BBOX)
    return partitioner._access_constraint_failures(
        graph, modifications, entries, CENTRE, target(**constraints), VehicleClass.CAR
    )


def spoke_closure(v, osm_id, windows=None):
    return StreetModification(
        u=CENTRE,
        v=v,
        osm_id=osm_id,
        modification_type=ModificationType.FULL_CLOSURE,
        time_windows=windows or [],
    )


def test_unconstrained_targets_are_not_rechecked():
    assert failures([spoke_closure(5, 200)], [5]) == []


def test_service_window_needs_a_path_during_that_window():
    timed = [
        spoke_closure(v, osm_id, ["Mo-Fr 07:30-08:30"])
        for v, osm_id in ((5, 200), (7, 201), (8, 203), (9, 202))
    ]
    entries = [5, 7, 8, 9]
    assert failures(timed, entries, service_windows=["Mo-Fr 10:00-12:00"]) == []
    reasons = failures(timed, entries, service_windows=["Mo-Fr 07:30-08:30"])
    assert reasons == ["No car inbound-and-return path during Mo-Fr 07:30-08:30"]


def test_unsurveyed_clearance_fails_conservatively():
    reasons = failures([], [5], minimum_clearance_m=3.5)
    assert reasons == ["Clearance is not surveyed on the car route"]


def test_detour_limit_compares_planned_and_baseline_routes():
    reasons = failures([spoke_closure(5, 200)], [5], maximum_detour_minutes=0.01)
    assert len(reasons) == 1
    assert reasons[0].startswith("Detour of ")
    assert failures([spoke_closure(5, 200)], [5], maximum_detour_minutes=60) == []
