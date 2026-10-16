import networkx as nx

from app.models.schemas import (
    ActiveModeRequirement,
    Coordinates,
    ModificationType,
    StreetModification,
    TransportMode,
)
from app.services.constraint.active_mode_validator import ActiveModeValidator


def active_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for node, x in ((1, 19.0), (2, 19.001), (3, 19.002)):
        graph.add_node(node, x=x, y=47.0)
    for u, v, osmid in ((1, 2, 10), (2, 3, 20)):
        data = {
            "osmid": osmid,
            "length": 100,
            "clear_width_m": 2.0,
            "grade": 0.02,
            "level_of_traffic_stress": 1,
        }
        graph.add_edge(u, v, key=0, **data)
        graph.add_edge(v, u, key=0, **data)
    return graph


def walk_requirement() -> ActiveModeRequirement:
    return ActiveModeRequirement(
        requirement_id="walk-1",
        mode="walk",
        origin=Coordinates(lat=47.0, lon=19.0),
        destination=Coordinates(lat=47.0, lon=19.002),
        minimum_clear_width_m=1.8,
        maximum_grade_percent=5,
        source="Accessibility survey",
    )


def test_accessible_modal_filter_preserves_walk_route():
    modification = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
        allowed_modes=[TransportMode.WALK, TransportMode.WHEEL, TransportMode.BICYCLE],
    )

    result = (
        ActiveModeValidator({"walk": active_graph()})
        .validate(
            [walk_requirement()],
            [modification],
        )
        .results[0]
    )

    assert result.passed is True
    assert result.planned_distance_m == 200
    assert result.geometry_attributes_passed is True


def test_filter_without_mode_permission_fails_closed():
    modification = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
    )

    result = (
        ActiveModeValidator({"walk": active_graph()})
        .validate(
            [walk_requirement()],
            [modification],
        )
        .results[0]
    )

    assert result.passed is False
    assert "removes this active-mode route" in result.blockers[0]
