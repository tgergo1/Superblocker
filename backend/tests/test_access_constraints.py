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
    assert reasons == ["No car route with surveyed clearance of at least 3.50 m"]


def test_clearance_accepts_a_slower_route_under_the_height_limit():
    graph = cell_graph()
    for _u, _v, data in graph.edges(data=True):
        data["clearance_m"] = 4.5
    # The direct spoke from entry 5 has a low bridge; the route from entry 8
    # is clear.
    graph[CENTRE][5][0]["clearance_m"] = 2.5
    graph[5][CENTRE][0]["clearance_m"] = 2.5
    partitioner = CityPartitioner(graph, BBOX)

    def check(minimum):
        return partitioner._access_constraint_failures(
            graph, [], [5, 8], CENTRE, target(minimum_clearance_m=minimum), VehicleClass.CAR
        )

    assert check(4.0) == []
    assert check(5.0) == ["No car route with surveyed clearance of at least 5.00 m"]


def test_detour_limit_compares_planned_and_baseline_routes():
    reasons = failures([spoke_closure(5, 200)], [5], maximum_detour_minutes=0.01)
    assert len(reasons) == 1
    assert reasons[0].startswith("Detour of ")
    assert failures([spoke_closure(5, 200)], [5], maximum_detour_minutes=60) == []


def test_target_served_outside_a_timed_closure_is_reachable():
    graph = cell_graph()
    partitioner = CityPartitioner(graph, BBOX)
    timed = [
        spoke_closure(v, osm_id, ["Mo-Fr 07:30-08:30"])
        for v, osm_id in ((5, 200), (7, 201), (8, 203), (9, 202))
    ]

    def unreachable(windows):
        return partitioner._find_unreachable_access_targets(
            graph, timed, [5, 7, 8, 9], None, [target(service_windows=windows)]
        )

    assert unreachable(["Mo-Fr 10:00-12:00"]) == []
    assert len(unreachable(["Mo-Fr 07:30-08:30"])) == 1
