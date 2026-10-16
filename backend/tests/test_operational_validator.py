import networkx as nx

from app.models.schemas import (
    Coordinates,
    ModificationType,
    OperationalRequirement,
    StreetModification,
    VehicleClass,
)
from app.services.constraint.operational_validator import OperationalValidator


def service_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for node, x in ((1, 19.0), (2, 19.001), (3, 19.002)):
        graph.add_node(node, x=x, y=47.0)
    for u, v, osmid in ((1, 2, 10), (2, 3, 20)):
        graph.add_edge(u, v, key=0, osmid=osmid, length=100, maxspeed=30)
        graph.add_edge(v, u, key=0, osmid=osmid, length=100, maxspeed=30)
    return graph


def requirement(
    requirement_id: str,
    service_type: str,
    vehicle_class: VehicleClass,
) -> OperationalRequirement:
    return OperationalRequirement(
        requirement_id=requirement_id,
        service_type=service_type,
        vehicle_class=vehicle_class,
        origin=Coordinates(lat=47.0, lon=19.0),
        destination=Coordinates(lat=47.0, lon=19.002),
        maximum_detour_percent=5,
        source="Operator route specification",
    )


def test_operational_validation_keeps_emergency_separate_from_bus_access():
    modal_filter = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
        allowed_vehicle_classes=[VehicleClass.EMERGENCY],
    )
    summary = OperationalValidator(service_graph()).validate(
        [
            requirement("bus-1", "transit_operation", VehicleClass.BUS),
            requirement("fire-1", "emergency_response", VehicleClass.EMERGENCY),
        ],
        [modal_filter],
    )

    results = {result.requirement_id: result for result in summary.results}
    assert results["bus-1"].passed is False
    assert "removes this service route" in results["bus-1"].blockers[0]
    assert results["fire-1"].passed is True
    assert summary.all_hard_constraints_passed is False
    assert summary.missing_service_types == ["freight_delivery", "refuse_collection"]


def test_clearance_requirement_fails_closed_when_geometry_is_missing():
    route = requirement("fire-clearance", "emergency_response", VehicleClass.EMERGENCY)
    route.minimum_clearance_m = 3.5

    result = OperationalValidator(service_graph()).validate([route], []).results[0]

    assert result.passed is False
    assert result.clearance_passed is None
    assert "clearance is missing" in result.blockers[-1]
